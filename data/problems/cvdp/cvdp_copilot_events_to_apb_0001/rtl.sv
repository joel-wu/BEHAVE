module apb_controller (
    input logic clk, reset_n, select_a_i, select_b_i, select_c_i,
    input logic [31:0] addr_a_i, data_a_i, addr_b_i, data_b_i, addr_c_i, data_c_i,
    input logic apb_pready_i,
    output logic apb_psel_o, apb_penable_o, apb_pwrite_o,
    output logic [31:0] apb_paddr_o, apb_pwdata_o
);
    logic [1:0] phase;
    logic [31:0] saved_addr, saved_data;
    logic [3:0] wait_count;
    assign apb_psel_o=(phase!=0);
    assign apb_pwrite_o=(phase!=0);
    assign apb_penable_o=(phase==2);
    assign apb_paddr_o=(phase!=0) ? saved_addr : 0;
    assign apb_pwdata_o=(phase!=0) ? saved_data : 0;
    always_ff @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin phase<=0; saved_addr<=0; saved_data<=0; wait_count<=0; end
        else case(phase)
            0: if(select_a_i || select_b_i || select_c_i) begin
                phase<=1; wait_count<=0;
                saved_addr<=select_a_i ? addr_a_i : (select_b_i ? addr_b_i : addr_c_i);
                saved_data<=select_a_i ? data_a_i : (select_b_i ? data_b_i : data_c_i);
            end
            1: phase<=2;
            2: if(apb_pready_i || wait_count==14) begin phase<=0; wait_count<=0; end
               else wait_count<=wait_count+1'b1;
            default: phase<=0;
        endcase
    end
endmodule
