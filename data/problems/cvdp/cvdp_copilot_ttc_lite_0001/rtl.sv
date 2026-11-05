module ttc_counter_lite (
    input logic clk, reset,
    input logic [3:0] axi_addr,
    input logic [31:0] axi_wdata,
    input logic axi_write_en, axi_read_en,
    output logic [31:0] axi_rdata,
    output logic interrupt
);
    logic [15:0] count, match_value, reload_value;
    logic enable, interval_mode, interrupt_enable;
    always_comb begin
        axi_rdata=0;
        if (axi_read_en) case (axi_addr)
            0: axi_rdata={16'b0,count};
            1: axi_rdata={16'b0,match_value};
            2: axi_rdata={16'b0,reload_value};
            3: axi_rdata={29'b0,interrupt_enable,interval_mode,enable};
            4: axi_rdata={31'b0,interrupt};
            default: ;
        endcase
    end
    always_ff @(posedge clk) begin
        if (reset) begin
            count<=0; match_value<=0; reload_value<=0;
            enable<=0; interval_mode<=0; interrupt_enable<=0; interrupt<=0;
        end else begin
            if (enable) begin
                if (count==match_value) begin
                    if (interval_mode) count<=reload_value;
                    if (interrupt_enable) interrupt<=1;
                end else count<=count+16'd1;
            end
            if (axi_write_en) case (axi_addr)
                1: match_value<=axi_wdata[15:0];
                2: reload_value<=axi_wdata[15:0];
                3: {interrupt_enable,interval_mode,enable}<=axi_wdata[2:0];
                4: interrupt<=0;
                default: ;
            endcase
        end
    end
endmodule
