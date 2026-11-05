module csr_apb_interface (
    input logic clk, rst,
    input logic pselx, penable, pwrite,
    input logic [31:0] paddr, pwdata,
    input logic overflow_is_set, sign_is_set, parity_is_set, zero_is_set,
    output logic [31:0] prdata,
    output logic pready, pslverr,
    output logic [1:0] debug_state
);
    typedef enum logic [1:0] {IDLE, SETUP, READ_STATE, WRITE_STATE} state_t;
    state_t state;
    logic [31:0] data_reg, control_reg, interrupt_reg, saved_addr, saved_data;
    logic [3:0] isr;
    logic saved_write;
    wire [3:0] events={zero_is_set,parity_is_set,sign_is_set,overflow_is_set};
    wire mapped=(saved_addr==16 || saved_addr==20 || saved_addr==24 || saved_addr==28);
    assign debug_state=state;
    assign pready=(state==READ_STATE || state==WRITE_STATE) && pselx && penable;
    assign pslverr=pready && (!mapped || (saved_write && saved_addr==28));
    always_comb begin
        prdata=0;
        if (pready && !saved_write) case (saved_addr)
            16: prdata=data_reg;
            20: prdata=control_reg;
            24: prdata=interrupt_reg;
            28: prdata={28'd0,isr};
            default: ;
        endcase
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            state<=IDLE; data_reg<=0; control_reg<=0; interrupt_reg<=0; isr<=0;
            saved_addr<=0; saved_data<=0; saved_write<=0;
        end else begin
            isr<=isr|events;
            if (pready) begin
                if (saved_write) case (saved_addr)
                    16: data_reg<=saved_data;
                    20: control_reg<=saved_data;
                    24: begin interrupt_reg<=saved_data; isr<=(isr|events)&~saved_data[3:0]; end
                    default: ;
                endcase
                state<=IDLE;
            end else if (!pselx) state<=IDLE;
            else if (!penable) begin
                saved_addr<=paddr; saved_data<=pwdata; saved_write<=pwrite; state<=SETUP;
            end else if (state==SETUP) state<=saved_write ? WRITE_STATE : READ_STATE;
        end
    end
endmodule
