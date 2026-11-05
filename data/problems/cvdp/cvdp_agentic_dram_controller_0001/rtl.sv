module dramcntrl #(
    parameter int del_width=15,
    parameter int len_auto_ref=10,
    parameter int len_small=8,
    parameter int addr_bits_to_dram=13,
    parameter int addr_bits_from_up=24,
    parameter int ba_bits=2
)(
    input logic clk_in_level, reset_level,
    input logic [addr_bits_from_up-1:0] addr_from_up,
    input logic rd_n_from_up, wr_n_from_up, bus_term_from_up,
    output logic [addr_bits_to_dram-1:0] addr,
    output logic [ba_bits-1:0] ba,
    output wire clk,
    output logic cke,
    output wire cs_n, ras_n, cas_n, we_n,
    output wire [1:0] dqm,
    output logic dram_init_done,
    output wire dram_busy,
    output logic rd_dat_from_dram_ready
);
    localparam int C=addr_bits_from_up-addr_bits_to_dram-ba_bits;
    logic [del_width-1:0] init_tick=0, refresh_tick=0;
    logic [len_auto_ref-1:0] pending=0, next_pending;
    logic [len_small-1:0] age=0;
    logic [2:0] phase=0;
    logic req_write=0, terminate=0, prev_active=0;
    logic [C-1:0] req_col=0;
    logic [5:0] command=63;
    wire active_request=!(rd_n_from_up && wr_n_from_up);
    wire due=refresh_tick==780;
    wire complete=phase==5 && age==7;
    initial begin
        addr=0; ba=0; cke=0; dram_init_done=0; rd_dat_from_dram_ready=0;
    end
    assign clk=clk_in_level;
    assign {cs_n,ras_n,cas_n,we_n,dqm}=command;
    assign dram_busy=!dram_init_done || phase!=0 || pending!=0;
    always_comb begin
        next_pending=pending;
        if(due && !complete && !(&pending)) next_pending=pending+1'b1;
        if(complete && !due && pending!=0) next_pending=pending-1'b1;
    end
    always_ff @(posedge clk_in_level) begin
        if(reset_level) begin
            init_tick<=0; refresh_tick<=0; pending<=0; phase<=0; age<=0;
            req_write<=0; req_col<=0; terminate<=0; prev_active<=0;
            command<=63; addr<=0; ba<=0; cke<=0;
            dram_init_done<=0; rd_dat_from_dram_ready<=0;
        end else begin
            prev_active<=active_request; cke<=1; command<=31;
            if(!dram_init_done) begin
                case(init_tick)
                    10000: begin command<=11; addr<=1024; end
                    10004,10012: command<=7;
                    10020: begin command<=3; addr<=39; end
                    10023: dram_init_done<=1;
                    default: ;
                endcase
                if(init_tick<10024) init_tick<=init_tick+1'b1;
            end else begin
                refresh_tick<=due ? 0 : refresh_tick+1'b1;
                pending<=next_pending;
                case(phase)
                    0: if(next_pending!=0) begin
                        command<=7; phase<=5; age<=0;
                    end else if(active_request && !prev_active && bus_term_from_up) begin
                        addr<=addr_from_up[C +: addr_bits_to_dram];
                        ba<=addr_from_up[C+addr_bits_to_dram +: ba_bits];
                        req_col<=addr_from_up[C-1:0]; req_write<=!wr_n_from_up;
                        terminate<=0; command<=15; phase<=1; age<=0;
                    end
                    1: begin
                        if(!active_request || !bus_term_from_up) terminate<=1;
                        if(age==1) begin
                            command<=req_write ? 16 : 20; addr<=req_col; phase<=2; age<=0;
                        end else age<=age+1'b1;
                    end
                    2: if(terminate || !active_request || !bus_term_from_up) begin
                        command<=27; rd_dat_from_dram_ready<=0; phase<=3;
                    end else begin
                        command<=28;
                        if(age>=1 && !req_write) rd_dat_from_dram_ready<=1;
                        if(age<2) age<=age+1'b1;
                    end
                    3: begin command<=11; addr<=0; phase<=4; age<=0; end
                    4: if(age==3) phase<=0; else age<=age+1'b1;
                    5: if(complete) phase<=0; else age<=age+1'b1;
                    default: phase<=0;
                endcase
            end
        end
    end
endmodule
