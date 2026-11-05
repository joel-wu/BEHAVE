module ttc_counter_lite (
    input logic clk, reset,
    input logic [3:0] axi_addr,
    input logic [31:0] axi_wdata,
    input logic axi_write_en, axi_read_en,
    output logic [31:0] axi_rdata,
    output logic interrupt
);
    logic [15:0] count, match_value, reload_value;
    logic [2:0] control;
    logic status;
    logic [3:0] prescaler, phase;
    wire pulse = control[0] && phase==prescaler;
    wire [15:0] incremented = count+16'd1;
    wire match_event = control[0] && ((count==match_value) ||
                                       (pulse && incremented==match_value));
    assign interrupt = status && control[2];

    always_ff @(posedge clk) begin
        if (reset) begin
            count<=0; match_value<=0; reload_value<=0; control<=0;
            status<=0; prescaler<=0; phase<=0; axi_rdata<=0;
        end else begin
            if (axi_read_en) begin
                case (axi_addr)
                    0: axi_rdata <= {16'd0,count};
                    1: axi_rdata <= {16'd0,match_value};
                    2: axi_rdata <= {16'd0,reload_value};
                    3: axi_rdata <= {29'd0,control};
                    4: axi_rdata <= {31'd0,status};
                    5: axi_rdata <= {28'd0,prescaler};
                    default: axi_rdata <= 0;
                endcase
            end
            if (!control[0]) phase<=0;
            else if (match_event) begin
                phase<=0;
                if (control[1]) count<=reload_value;
                else begin
                    count<=(count==match_value) ? count : incremented;
                    control[0]<=0;
                end
            end else if (pulse) begin phase<=0; count<=incremented; end
            else phase<=phase+1'b1;

            if (axi_write_en) begin
                case (axi_addr)
                    1: match_value<=axi_wdata[15:0];
                    2: reload_value<=axi_wdata[15:0];
                    3: begin
                        control<=axi_wdata[2:0];
                        if (!axi_wdata[0]) phase<=0;
                    end
                    4: if (axi_wdata[0]) status<=0;
                    5: begin prescaler<=axi_wdata[3:0]; phase<=0; end
                    default: ;
                endcase
            end
            // A fresh event wins over simultaneous software write-one-clear.
            if (match_event) status<=1;
        end
    end
endmodule
